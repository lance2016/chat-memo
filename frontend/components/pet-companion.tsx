"use client";

import { useEffect, useRef, useState } from "react";
import { Hand, Heart, MoonStar, Sparkles, Sun, X } from "lucide-react";
import { useI18n } from "@/components/i18n-provider";

type PetMotion = "idle" | "wave" | "happy" | "sleep";

export function PetCompanion() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [motion, setMotion] = useState<PetMotion>("idle");
  const [message, setMessage] = useState(t("pet.status.ready"));
  const shellRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<number | null>(null);

  const react = (next: PetMotion, nextMessage: string) => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    setMotion(next);
    setMessage(nextMessage);
    if (next === "sleep") return;
    timerRef.current = window.setTimeout(() => {
      setMotion("idle");
      setMessage(t("pet.status.ready"));
    }, next === "happy" ? 1800 : 1400);
  };

  useEffect(() => () => { if (timerRef.current !== null) window.clearTimeout(timerRef.current); }, []);
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!shellRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", escape); };
  }, [open]);

  const openMenu = () => {
    setOpen((value) => !value);
    if (motion === "sleep") react("idle", t("pet.status.awake"));
    else react("wave", t("pet.status.hello"));
  };

  return <div className={`desktop-pet motion-${motion} ${open ? "is-open" : ""}`} ref={shellRef}>
    {open && <section className="desktop-pet-menu" aria-label={t("pet.menu.label")}>
      <header>
        <span className="desktop-pet-avatar"><Sparkles size={14} /></span>
        <span><strong>{t("pet.name")}</strong><small><i />{message}</small></span>
        <button className="desktop-pet-close" type="button" aria-label={t("pet.menu.close")} onClick={() => setOpen(false)}><X size={14} /></button>
      </header>
      <div className="desktop-pet-actions">
        <button type="button" onClick={() => react("wave", t("pet.status.hello"))}><Hand size={16} /><span><strong>{t("pet.action.wave")}</strong><small>{t("pet.action.waveHint")}</small></span></button>
        <button type="button" onClick={() => react("happy", t("pet.status.patted"))}><Heart size={16} /><span><strong>{t("pet.action.pat")}</strong><small>{t("pet.action.patHint")}</small></span></button>
        {motion === "sleep"
          ? <button type="button" onClick={() => react("idle", t("pet.status.awake"))}><Sun size={16} /><span><strong>{t("pet.action.wake")}</strong><small>{t("pet.action.wakeHint")}</small></span></button>
          : <button type="button" onClick={() => { react("sleep", t("pet.status.sleeping")); setOpen(false); }}><MoonStar size={16} /><span><strong>{t("pet.action.sleep")}</strong><small>{t("pet.action.sleepHint")}</small></span></button>}
      </div>
    </section>}
    <button className="desktop-pet-character" type="button" aria-label={t("pet.open")} aria-haspopup="menu" aria-expanded={open} onClick={openMenu}>
      <span className="desktop-pet-sprite" aria-hidden="true" />
    </button>
    <span className="desktop-pet-floor" aria-hidden="true" />
  </div>;
}
