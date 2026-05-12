// Track the iOS soft-keyboard height and write it to a CSS variable on
// <body> so the composer / sticky-bottom UI can translate above it.
//
// Browsers don't expose the keyboard directly. We read it from
// `visualViewport`: when the keyboard opens, the visual viewport
// height shrinks while the layout height stays the same; the
// difference is roughly the keyboard. Subtracting `offsetTop` accounts
// for browser UI scrolling.
//
// Only runs on mobile-class viewports (matches `(max-width: 1023px)`),
// since desktops never trigger this and we don't want to pay the
// resize-listener cost or risk a non-zero inset on a misbehaving
// browser. Variable resets to 0px on unmount.

"use client";

import { useEffect } from "react";

const VAR = "--abs-keyboard-inset";

export function useKeyboardInset(enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    if (typeof window === "undefined" || !window.visualViewport) return;

    // Cheap mobile gate. iPad + Safari hits this too — that's fine,
    // they have soft keyboards as well. Anything ≥1024px is a desktop
    // browser and we leave the inset at 0.
    if (!window.matchMedia("(max-width: 1023px)").matches) return;

    const vv = window.visualViewport;
    const root = document.body;

    const update = () => {
      const inset = Math.max(
        0,
        window.innerHeight - vv.height - vv.offsetTop,
      );
      root.style.setProperty(VAR, `${Math.round(inset)}px`);
    };

    update();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);

    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
      root.style.setProperty(VAR, "0px");
    };
  }, [enabled]);
}
