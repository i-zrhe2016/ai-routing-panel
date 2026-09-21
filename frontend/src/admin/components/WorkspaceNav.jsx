const ICONS = {
  overview: "M4 13h6V4H4v9Zm0 7h6v-5H4v5Zm10 0h6v-9h-6v9Zm0-16v5h6V4h-6Z",
  hosts: "M5 4h14v6H5V4Zm0 10h14v6H5v-6Zm3-7h.01M8 17h.01",
  traffic: "M4 17V9m5 8V5m5 12v-6m5 6V3",
  diagnostics: "M12 8v5m0 3h.01M10.3 3.9 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  routing: "M4 6h6l2 3h8M4 18h6l2-3h8M12 9v6",
  delivery: "M4 6h16M4 12h16M4 18h10",
  commerce: "M5 7h14l-1 12H6L5 7Zm3 0V5a4 4 0 0 1 8 0v2",
  observe: "M4 15l4-4 3 3 5-7 4 4M4 20h16",
};

export default function WorkspaceNav({ items, activeKey, onSelect, badges = {} }) {
  return (
    <nav className="workspace-nav" aria-label="控制台工作区">
      <p className="nav-label">CONTROL CENTER</p>
      {items.map((item) => (
        <button
          key={item.key}
          className={`workspace-nav__item${activeKey === item.key ? " is-active" : ""}`}
          type="button"
          aria-current={activeKey === item.key ? "page" : undefined}
          onClick={() => onSelect(item.key)}
        >
          <span className="workspace-nav__icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d={ICONS[item.key] || "M5 12h14"} />
            </svg>
          </span>
          <span className="workspace-nav__copy">
            <strong>{item.label}</strong>
            <small>{item.description}</small>
          </span>
          {badges[item.key] ? <span className="workspace-nav__badge">{badges[item.key]}</span> : null}
        </button>
      ))}
    </nav>
  );
}
