/**
 * Keyboard focus and reduced motion on the trading dashboard.
 *
 * Reads computed styles rather than taking screenshots: the whole risk here is that
 * one CSS edit, or one more `focus:outline-none` utility, silently removes focus
 * indication across the app — and that is invisible in a screenshot of an unfocused
 * page. Ten of those utilities already exist, which is why the ring is a box-shadow.
 *
 * ⚠ Do NOT add `isMobile: true` to the context. Touch emulation suppresses
 * :focus-visible, so every check here would pass against a ring that does not exist.
 * The cashflow suite learned this the hard way.
 *
 *   npx vite preview --port 4174 --strictPort &
 *   node e2e/a11y.test.mjs
 */
import { createRequire } from "node:module";

const require = createRequire("C:/Users/kenne/twodo/package.json");
const { chromium } = require("playwright");

const APP = "http://localhost:4174";

let passed = 0;
const failed = [];

function check(label, condition) {
  if (condition) {
    passed++;
    console.log(`  PASS  ${label}`);
  } else {
    failed.push(label);
    console.log(`  FAIL  ${label}`);
  }
}

const browser = await chromium.launch();

// ── focus rings ───────────────────────────────────────────────────────────────
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await context.newPage();
await page.goto(APP);
await page.waitForTimeout(1500);

console.log("\n[1] keyboard focus is visible");

const ringOf = (sel) =>
  page.evaluate((s) => {
    const el = document.querySelector(s);
    if (!el) return null;
    el.focus();
    return getComputedStyle(el).boxShadow;
  }, sel);

// Tab from the top of the document — this is the real keyboard path, not a
// synthetic .focus() on a hand-picked node.
//
// Read on the frame focus arrives, with no settling pause. That is deliberate: the
// first version of this ring was covered by `transition-all` and animated in from
// 0.13px/3% opacity, so a test that waited would have called an invisible ring a
// pass. A focus indicator is either there when focus lands or it is not.
await page.keyboard.press("Tab");
const firstTabbed = await page.evaluate(() => {
  const el = document.activeElement;
  return {
    tag: el?.tagName,
    shadow: el ? getComputedStyle(el).boxShadow : null,
    isBody: el === document.body,
  };
});
check("Tab moves focus off <body>", !firstTabbed.isBody);
check(
  `first tabbed element (${firstTabbed.tag}) paints a ring`,
  !!firstTabbed.shadow && firstTabbed.shadow !== "none",
);
check(
  "the ring uses the dedicated focus hue, not a semantic colour",
  (firstTabbed.shadow || "").includes("77, 163, 255"),
);

// Semantic colours must not be borrowed — those mean something on this dashboard.
for (const [name, rgb] of [["green", "0, 200, 5"], ["red", "255, 59, 48"], ["orange", "255, 149, 0"]]) {
  check(`the ring is not the ${name} data colour`, !(firstTabbed.shadow || "").includes(rgb));
}

// Walk further into the page and confirm every focusable stop is indicated.
let unringed = [];
for (let i = 0; i < 25; i++) {
  await page.keyboard.press("Tab");
  const r = await page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const cs = getComputedStyle(el);
    return {
      tag: el.tagName,
      cls: (el.className || "").toString().slice(0, 40),
      shadow: cs.boxShadow,
      outline: cs.outlineStyle,
    };
  });
  if (r && (!r.shadow || r.shadow === "none") && r.outline === "none") {
    unringed.push(`${r.tag}.${r.cls}`);
  }
}
check(
  `every tab stop is indicated (${unringed.length} bare)`,
  unringed.length === 0,
);
if (unringed.length) console.log("        bare:", unringed.slice(0, 5).join(", "));

console.log("\n[2] a mouse click leaves no ring behind");
// A real click through Playwright, not a dispatched MouseEvent: :focus-visible keys
// off trusted input, so a synthetic event is still treated as keyboard-ish and the
// button comes back matching. That is a property of the test, not of the CSS.
const btn = page.locator("button").first();
await btn.click();
// Settle first. A ring vanishing is allowed to animate — `transition: none` applies
// only while :focus-visible matches, so on the way out the button's own
// `transition-all` takes over and the colour is briefly still in the computed value.
// Appearing late is an accessibility failure; disappearing late is not.
await page.waitForTimeout(500);
const clicked = await page.evaluate(() => {
  const el = document.activeElement;
  if (!el || el.tagName !== "BUTTON") return null;
  return { matches: el.matches(":focus-visible"), shadow: getComputedStyle(el).boxShadow };
});
check("a button clicked with the mouse is not :focus-visible", clicked && !clicked.matches);
check(
  "and so paints no ring",
  clicked && (clicked.shadow === "none" || !clicked.shadow.includes("77, 163, 255")),
);

// The same button, reached by keyboard, must ring — otherwise the check above passes
// for the wrong reason (a button that never rings at all).
await page.keyboard.press("Tab");
await page.keyboard.press("Shift+Tab");
const viaKeyboard = await page.evaluate(() => {
  const el = document.activeElement;
  return el && el !== document.body
    ? { matches: el.matches(":focus-visible"), shadow: getComputedStyle(el).boxShadow }
    : null;
});
check(
  "the same route by keyboard does ring",
  viaKeyboard && viaKeyboard.matches && viaKeyboard.shadow.includes("77, 163, 255"),
);

console.log("\n[3] inputs with focus:outline-none still get a ring");
const inputRing = await page.evaluate(() => {
  // The utility sets a transparent outline; the ring must not depend on outline.
  const el = document.createElement("input");
  el.className = "focus:outline-none";
  document.body.appendChild(el);
  el.focus();
  const cs = getComputedStyle(el);
  const out = { shadow: cs.boxShadow, outlineColor: cs.outlineColor };
  el.remove();
  return out;
});
check(
  "box-shadow ring survives focus:outline-none",
  !!inputRing.shadow && inputRing.shadow !== "none" && inputRing.shadow.includes("77, 163, 255"),
);

await context.close();

// ── reduced motion ────────────────────────────────────────────────────────────
console.log("\n[4] prefers-reduced-motion is honoured");
const rm = await browser.newContext({
  viewport: { width: 1280, height: 900 },
  reducedMotion: "reduce",
});
const rmPage = await rm.newPage();
await rmPage.goto(APP);
await rmPage.waitForTimeout(1200);

const durations = await rmPage.evaluate(() => {
  const out = { slowTransitions: 0, slowAnimations: 0, sampled: 0 };
  for (const el of document.querySelectorAll("*")) {
    const cs = getComputedStyle(el);
    out.sampled++;
    for (const d of cs.transitionDuration.split(",")) {
      if (parseFloat(d) > 0.001) out.slowTransitions++;
    }
    for (const d of cs.animationDuration.split(",")) {
      if (parseFloat(d) > 0.001) out.slowAnimations++;
    }
  }
  return out;
});
check(`sampled a real page (${durations.sampled} elements)`, durations.sampled > 50);
check(`no transition still runs (${durations.slowTransitions})`, durations.slowTransitions === 0);
check(`no animation still runs (${durations.slowAnimations})`, durations.slowAnimations === 0);

// Control: without the preference, motion is still there — otherwise the check above
// would pass just as well against an app that has no transitions at all.
const normal = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const nPage = await normal.newPage();
await nPage.goto(APP);
await nPage.waitForTimeout(1200);
const motionOn = await nPage.evaluate(() => {
  let n = 0;
  for (const el of document.querySelectorAll("*")) {
    for (const d of getComputedStyle(el).transitionDuration.split(",")) {
      if (parseFloat(d) > 0.001) n++;
    }
  }
  return n;
});
check(`motion is present without the preference (${motionOn})`, motionOn > 0);

console.log(`\n${passed} passed, ${failed.length} failed`);
if (failed.length) console.log(failed.map((f) => `  - ${f}`).join("\n"));

await browser.close();
process.exit(failed.length ? 1 : 0);
