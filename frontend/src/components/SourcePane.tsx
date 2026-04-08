import type { CitationReport } from "../types";
import CitationCard from "./CitationCard";
import styles from "./SourcePane.module.css";

interface Props {
  citation: CitationReport | null;
  onClose: () => void;
}

export default function SourcePane({ citation, onClose }: Props) {
  if (!citation) {
    return (
      <div className={styles.empty}>
        <p className={styles.emptyIcon}>←</p>
        <p>Click a highlighted citation in the document to see verification details.</p>
      </div>
    );
  }

  return (
    <div className={styles.pane}>
      <div className={styles.header}>
        <h3 className={styles.caseName}>
          {citation.extraction.case_name || citation.extraction.citation_text}
        </h3>
        <button className={styles.closeBtn} onClick={onClose}>×</button>
      </div>
      <CitationCard citation={citation} />
    </div>
  );
}
