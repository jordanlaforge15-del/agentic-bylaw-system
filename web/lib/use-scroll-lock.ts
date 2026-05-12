// While `locked === true`, prevent the document body from scrolling.
// Used by Drawer + Sheet so the chat thread underneath doesn't scroll
// when the user drags inside an overlay. Restores the original style
// (and scroll position) on unmount.
//
// Important: we save and restore the inline `overflow` rather than
// blindly setting it to "" — if the page already had a non-default
// overflow we'd be silently overwriting it.

"use client";

import { useEffect } from "react";

export function useScrollLock(locked: boolean) {
  useEffect(() => {
    if (!locked) return;
    const body = document.body;
    const prev = body.style.overflow;
    const prevTouchAction = body.style.touchAction;
    body.style.overflow = "hidden";
    body.style.touchAction = "none";
    return () => {
      body.style.overflow = prev;
      body.style.touchAction = prevTouchAction;
    };
  }, [locked]);
}
