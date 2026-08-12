import { useRef, useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useDialogFocus } from "@/lib/use-dialog-focus";

function DialogHarness() {
  const [open, setOpen] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useDialogFocus({ dialogRef, initialFocusRef: closeRef, onClose: () => setOpen(false), enabled: open });

  return <>
    <button type="button" onClick={() => setOpen(true)}>Open dialog</button>
    {open && <section ref={dialogRef} role="dialog" tabIndex={-1}>
      <button ref={closeRef} type="button" onClick={() => setOpen(false)}>Close dialog</button>
      <button type="button">Confirm</button>
    </section>}
  </>;
}

afterEach(() => {
  document.body.style.overflow = "";
});

describe("useDialogFocus", () => {
  it("traps focus, closes on Escape, restores scrolling and returns focus", async () => {
    document.body.style.overflow = "clip";
    render(<DialogHarness />);

    const trigger = screen.getByRole("button", { name: "Open dialog" });
    trigger.focus();
    fireEvent.click(trigger);

    const close = screen.getByRole("button", { name: "Close dialog" });
    const confirm = screen.getByRole("button", { name: "Confirm" });
    await waitFor(() => expect(close).toHaveFocus());
    expect(document.body.style.overflow).toBe("hidden");

    confirm.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(close).toHaveFocus();

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(confirm).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("clip");
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
