import { useWorkspace, workspace, type Activity } from "../state/useWorkspace";

const ITEMS: { id: Activity; label: string; icon: JSX.Element }[] = [
  {
    id: "texts",
    label: "Texts",
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
          fill="#12111a"
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
