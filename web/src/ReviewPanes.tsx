import { useState, type ReactNode } from "react";
import { FileCheck2, Files } from "lucide-react";

export default function ReviewPanes({ children }: { children: ReactNode }) {
  const [pane, setPane] = useState<"review" | "papers">("review");

  return (
    <div className="review-layout">
      <div className="review-switch" role="group" aria-label="合同工作区视图">
        <button aria-pressed={pane === "review"} onClick={() => setPane("review")}>
          <FileCheck2 size={16} /> 审查工作区
        </button>
        <button aria-pressed={pane === "papers"} onClick={() => setPane("papers")}>
          <Files size={16} /> 审查底稿
        </button>
      </div>
      <div className="panels" data-pane={pane}>{children}</div>
    </div>
  );
}
