import { useWorkspace, workspace, type Activity } from "../state/useWorkspace";

const ITEMS: { id: Activity; label: string; icon: JSX.Element }[] = [
  {
    id: "catalog",
    label: "Catalog",
    icon: (
      <svg width={16} height={16} viewBox="0 0 16 16" fill="none">
        <rect
          x="2"
          y="2.5"
          width="12"
          height="2.5"
          rx=".5"
          stroke="currentColor"
          strokeWidth="1.2"
        />
        <rect
          x="2"
          y="6.75"
          width="12"
          height="2.5"
          rx=".5"
          stroke="currentColor"
          strokeWidth="1.2"
        />
        <rect
          x="2"
          y="11"
          width="12"
          height="2.5"
          rx=".5"
          stroke="currentColor"
          strokeWidth="1.2"
        />
      </svg>
    ),
  },
  {
    id: "timeline",
    label: "Timeline",
    icon: (
      <svg width={16} height={16} viewBox="0 0 16 16" fill="none">
        <path
          d="M8 2v12M4 4h8M5 8h6M3.5 12h9"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
        />
        <circle cx="8" cy="4" r="1.2" fill="currentColor" />
        <circle cx="8" cy="8" r="1.2" fill="currentColor" />
        <circle cx="8" cy="12" r="1.2" fill="currentColor" />
      </svg>
    ),
  },
  {
    id: "texts",
    label: "Contents",
    icon: (
      <svg width={16} height={16} viewBox="0 0 16 16" fill="none">
        <rect
          x="2"
          y="2"
          width="8"
          height="11"
          rx="1"
          stroke="currentColor"
          strokeWidth="1.3"
        />
        <path
          d="M5 6h4M5 8.5h3M5 11h2"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
        />
        <rect
          x="6"
          y="4"
          width="8"
          height="10"
          rx="1"
          fill="var(--bg-act)"
          stroke="currentColor"
          strokeWidth="1.3"
        />
        <path
          d="M9 8h2M9 10h3"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    id: "overlays",
    label: "Overlays",
    icon: (
      <svg width={16} height={16} viewBox="0 0 16 16" fill="none">
        <path
          d="M3 5.5 8 2.8l5 2.7-5 2.7-5-2.7Z"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinejoin="round"
        />
        <path
          d="m3 8 5 2.7L13 8M3 10.5l5 2.7 5-2.7"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
  {
    id: "history",
    label: "History",
    icon: (
      <svg width={16} height={16} viewBox="0 0 16 16" fill="none">
        <path
          d="M3 3.5v3h3M3.4 6.5A5 5 0 1 0 5 3.1"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M8 5.2v3.1l2.1 1.3"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
  {
    id: "lists",
    label: "Lists",
    icon: (
      <svg width={16} height={16} viewBox="0 0 16 16" fill="none">
        <path
          d="M5 4h8M5 8h8M5 12h8"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
        />
        <path
          d="M2.5 4h.01M2.5 8h.01M2.5 12h.01"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    id: "settings",
    label: "Settings",
    icon: (
      <svg width={16} height={16} viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="8" r="2.1" stroke="currentColor" strokeWidth="1.3" />
        <path
          d="M8 1.8v2M8 12.2v2M3.6 3.6 5 5M11 11l1.4 1.4M1.8 8h2M12.2 8h2M3.6 12.4 5 11M11 5l1.4-1.4"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
];

export function ActivityBar() {
  const activity = useWorkspace((s) => s.activity);
  return (
    <div className="act">
      {ITEMS.map((it) => (
        <button
          key={it.id}
          className={`act-btn${activity === it.id ? " on" : ""}`}
          onClick={() => workspace.setActivity(it.id)}
          title={it.label}
        >
          {it.icon}
          <span className="act-lbl">{it.label}</span>
        </button>
      ))}
    </div>
  );
}
