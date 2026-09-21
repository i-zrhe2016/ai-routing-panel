// In-page confirmation for destructive actions. Replaces window.confirm so the
// console can keep its visual language and so tests can drive the dialog.
import { useCallback, useRef, useState } from "react";

export function ConfirmDialog({ request, onCancel, onConfirm, busy }) {
  const dialogRef = useRef(null);
  if (!request) return null;
  const tone = request.tone || "primary";
  return (
    <div className="cc-modal-backdrop" onClick={(event) => event.target === dialogRef.current && onCancel()}>
      <div className="cc-modal" role="dialog" aria-modal="true" aria-labelledby="cc-confirm-title" ref={dialogRef}>
        <p className="section-kicker">CONFIRM ACTION</p>
        <h3 id="cc-confirm-title">{request.title}</h3>
        <p>{request.body}</p>
        <div className="cc-modal__actions">
          <button className="a-btn ghost" type="button" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button
            className={`a-btn ${tone === "danger" ? "danger" : "primary"}`}
            type="button"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "处理中…" : request.confirmLabel || "确认"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function useConfirm() {
  const [request, setRequest] = useState(null);
  const [busy, setBusy] = useState(false);

  const ask = useCallback((next) => setRequest(next), []);
  const close = useCallback(() => setRequest(null), []);
  const confirm = useCallback(async () => {
    if (!request) return;
    setBusy(true);
    try {
      await request.onConfirm();
    } finally {
      setBusy(false);
      setRequest(null);
    }
  }, [request]);

  const dialog = (
    <ConfirmDialog request={request} onCancel={close} onConfirm={confirm} busy={busy} />
  );

  return { ask, dialog, close };
}
