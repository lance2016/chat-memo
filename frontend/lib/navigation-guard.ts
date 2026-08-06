"use client";

import { useEffect } from "react";

const NAVIGATION_EVENT = "personal-ai-assistant:before-navigation";

/** Returns false when the active page vetoes an in-app navigation. */
export function confirmAppNavigation() {
  if (typeof window === "undefined") return true;
  return window.dispatchEvent(new Event(NAVIGATION_EVENT, { cancelable: true }));
}

/** Protects unsaved work from browser unloads and navigation initiated by app links. */
export function useNavigationGuard(active: boolean, message: string) {
  useEffect(() => {
    if (!active) return;

    const handleAppNavigation = (event: Event) => {
      if (!window.confirm(message)) event.preventDefault();
    };
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };

    window.addEventListener(NAVIGATION_EVENT, handleAppNavigation);
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => {
      window.removeEventListener(NAVIGATION_EVENT, handleAppNavigation);
      window.removeEventListener("beforeunload", handleBeforeUnload);
    };
  }, [active, message]);
}
