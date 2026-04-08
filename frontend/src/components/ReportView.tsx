import { useState } from "react";
import type { VerificationReport } from "../types";
import SummaryBar from "./SummaryBar";
import DocumentPane from "./DocumentPane";
import SourcePane from "./SourcePane";
import styles from "./ReportView.module.css";

interface Props {
  report: VerificationReport;
  onReset: () => void;
}

export default function ReportView({ report, onReset }: Props) {
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  const selectedCitation = selectedIndex !== null ? report.citations[selectedIndex] ?? null : null;

  return (
    <div className={styles.wrapper}>
      <SummaryBar report={report} onReset={onReset} />
      <div className={styles.panes}>
        <div className={styles.left}>
          <DocumentPane
            report={report}
            selectedIndex={selectedIndex}
            onSelectCitation={setSelectedIndex}
          />
        </div>
        <div className={styles.divider} />
        <div className={styles.right}>
          <SourcePane
            citation={selectedCitation}
            onClose={() => setSelectedIndex(null)}
          />
        </div>
      </div>
    </div>
  );
}
