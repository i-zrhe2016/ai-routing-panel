// Shared presentational primitives for the console. Kept deliberately small:
// every visual decision lives in admin.css so the token layer stays the single
// source of truth for colour and spacing.
import { useEffect, useRef, useState } from "react";

import { copyText } from "../../shared/clipboard.js";
import { TONES, statusTone } from "../../shared/tokens.js";

export function StatusPill({ label, tone, status }) {
  const effective = tone || statusTone(status);
  const palette = TONES[effective] || TONES.neutral;
  return (
    <span className="status-pill" style={{ color: palette.color, backgroundColor: palette.soft }}>
      <span className="status-pill__dot" style={{ backgroundColor: palette.color }} />
      {label || status}
    </span>
  );
}

export function MetricCard({ label, value, note, tone = "neutral", accent = false }) {
  return (
    <article className={`cc-metric is-${tone}${accent ? " is-accent" : ""}`}>
      <span className="cc-metric__label">{label}</span>
      <strong>{value}</strong>
      {note ? <small>{note}</small> : null}
    </article>
  );
}

export function Panel({ id, kicker, title, description, actions, children, className = "" }) {
  return (
    <section id={id} className={`cc-card ${className}`.trim()}>
      {title || kicker ? (
        <div className="cc-card__head">
          <div>
            {kicker ? <p className="section-kicker">{kicker}</p> : null}
            {title ? <h3>{title}</h3> : null}
            {description ? <p>{description}</p> : null}
          </div>
          {actions ? <div className="cc-card__actions">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function Notice({ message, level = "info", onClose }) {
  if (!message) return null;
  return (
    <div className={`admin-notice is-${level}`} role="status" aria-live="polite">
      <span className="admin-notice__mark" aria-hidden="true">{level === "error" ? "!" : "i"}</span>
      <span>{message}</span>
      <button className="notice-close" type="button" aria-label="关闭提示" onClick={onClose}>
        关闭
      </button>
    </div>
  );
}

export function Loader({ show, label = "正在加载控制面数据…" }) {
  if (!show) return null;
  return (
    <div className="cc-loader" role="status" aria-live="polite">
      <span className="cc-loader__spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({ children }) {
  return <div className="cc-empty">{children}</div>;
}

export function CopyField({ value, label, onCopied, onError }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  async function onCopy() {
    try {
      await copyText(value);
      setCopied(true);
      if (onCopied) onCopied(value);
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1200);
    } catch (error) {
      if (onError) onError(error);
    }
  }

  return (
    <div className="copy-field">
      {label ? <span className="copy-field__label">{label}</span> : null}
      <div className="copy-field__row">
        <input className="copy-field__value" value={value} data-copy-value={value} readOnly />
        <button type="button" className="copy-field__btn" onClick={onCopy}>
          {copied ? "已复制" : "复制"}
        </button>
      </div>
    </div>
  );
}

export function DataTable({ columns, rows, rowKey, empty, caption }) {
  if (!rows.length) {
    return <EmptyState>{empty || "暂无数据。"}</EmptyState>;
  }
  return (
    <div className="cc-table-wrap">
      <table className="cc-table">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col">{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={rowKey ? rowKey(row, index) : index}>
              {columns.map((column) => (
                <td key={column.key} className={column.className || undefined}>
                  {column.render ? column.render(row) : row[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Tone({ tone = "neutral", children }) {
  return <span className={`cc-tone is-${tone}`}>{children}</span>;
}
