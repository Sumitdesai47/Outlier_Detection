import { Info } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { failedEnginesForPoint, simpleReasonText } from "@/lib/simpleReason";
import type { ResultPoint } from "@/types/results";

export function ReasonWithEngines({ point }: { point: ResultPoint }) {
  const summary = simpleReasonText(point);
  const engines = failedEnginesForPoint(point);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showTooltip = useCallback(() => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    const rect = btnRef.current?.getBoundingClientRect();
    if (rect) {
      setPos({
        top: Math.min(rect.bottom + 6, window.innerHeight - 200),
        left: Math.min(rect.left, window.innerWidth - 280),
      });
    }
    setOpen(true);
  }, []);

  const hideTooltip = useCallback(() => {
    closeTimer.current = setTimeout(() => setOpen(false), 120);
  }, []);

  const cancelHide = useCallback(() => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
  }, []);

  return (
    <div className="flex max-w-lg items-start gap-2">
      <p className="flex-1 text-sm leading-relaxed text-foreground">{summary}</p>
      <button
        ref={btnRef}
        type="button"
        className="mt-0.5 shrink-0 rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label="Show which checks failed"
        onMouseEnter={showTooltip}
        onMouseLeave={hideTooltip}
        onFocus={showTooltip}
        onBlur={hideTooltip}
        onClick={() => (open ? setOpen(false) : showTooltip())}
      >
        <Info className="h-4 w-4" />
      </button>
      {open &&
        createPortal(
          <div
            className="fixed z-[9999] w-72 rounded-md border bg-white p-3 text-left text-xs leading-relaxed text-slate-800 shadow-lg"
            style={{ top: pos.top, left: pos.left }}
            onMouseEnter={cancelHide}
            onMouseLeave={hideTooltip}
            role="tooltip"
          >
            <p className="mb-2 font-semibold text-slate-900">Checks that failed</p>
            {engines.length ? (
              <ul className="list-decimal space-y-1.5 pl-4">
                {engines.map((e) => (
                  <li key={e.id}>
                    <span className="font-medium">{e.label}</span>
                    {e.detail ? <span className="text-slate-600"> — {e.detail}</span> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-slate-600">
                No check details stored. Re-run analysis to capture engine breakdown.
              </p>
            )}
          </div>,
          document.body,
        )}
    </div>
  );
}
