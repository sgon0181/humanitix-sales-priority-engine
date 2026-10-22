"use strict";
// Click-through introduction in front of the live demo. Presentation only: no demo or engine behaviour changes.
(() => {
  const root = document.documentElement;
  const ids = ["problem", "approach", "decisions", "demo"];
  const DEMO = ids.length - 1;
  const fromHash = () => Math.max(0, ids.indexOf(location.hash.slice(1)));
  root.classList.add(fromHash() === DEMO ? "show-demo" : "presenting");

  document.addEventListener("DOMContentLoaded", () => {
    const deck = document.getElementById("deck");
    const slides = Array.from(deck.querySelectorAll(".slide"));
    const dots = Array.from(document.querySelectorAll(".deck-dots button"));
    const demoParts = Array.from(document.querySelectorAll("body > header, body > main, body > footer"));
    let current = -1;

    function go(index) {
      index = Math.min(DEMO, Math.max(0, index));
      if (index === current) return;
      const onDemo = index === DEMO;
      current = index;
      slides.forEach((slide, i) => {
        slide.classList.toggle("active", i === index);
        slide.setAttribute("aria-hidden", String(i !== index));
      });
      dots.forEach((dot, i) => dot.toggleAttribute("aria-current", i === index));
      deck.dataset.slide = ids[index];
      root.classList.toggle("on-approach", ids[index] === "approach");
      root.classList.toggle("presenting", !onDemo);
      root.classList.toggle("show-demo", onDemo);
      deck.inert = onDemo;
      demoParts.forEach((part) => { part.inert = !onDemo; });
      if (location.hash.slice(1) !== ids[index]) history.replaceState(null, "", "#" + ids[index]);
      if (onDemo) window.scrollTo(0, 0);
    }

    deck.addEventListener("click", (event) => {
      if (event.target.closest("a, button")) return;
      if (String(window.getSelection())) return;
      go(current + 1);
    });
    dots.forEach((dot, i) => dot.addEventListener("click", () => go(i)));
    window.addEventListener("hashchange", () => go(fromHash()));
    document.addEventListener("keydown", (event) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.target.closest("input, textarea, select, [contenteditable]")) return;
      if (event.key === " " && event.target.closest("a, button")) return;
      if (current !== DEMO) {
        const step = {ArrowRight: 1, " ": 1, PageDown: 1, ArrowLeft: -1, PageUp: -1}[event.key];
        if (event.key === "Home") go(0);
        else if (event.key === "End") go(DEMO);
        else if (step) go(current + step);
        else return;
        event.preventDefault();
      } else if (event.key === "ArrowLeft" && (event.target === document.body || event.target === document.documentElement)) {
        // Inside the demo, arrows only leave the page when nothing (such as the scrollable table) has focus.
        event.preventDefault();
        go(DEMO - 1);
      }
    });
    go(fromHash());
  });
})();
