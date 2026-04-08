import { useEffect, useRef, useState } from "react";
import client from "../api/client";
import type { JobStatus, VerificationReport } from "../types";
import styles from "./ProgressPanel.module.css";

interface Props {
  jobId: string;
  onComplete: (status: JobStatus, report: VerificationReport) => void;
  onFailed: (status: JobStatus) => void;
  onReset: () => void;
  failedStatus: JobStatus | null;
}

export default function ProgressPanel({ jobId, onComplete, onFailed, onReset, failedStatus }: Props) {
  const [status, setStatus] = useState<JobStatus | null>(failedStatus);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (failedStatus) return; // Don't poll if we already have a failure

    async function poll() {
      try {
        const resp = await client.get<JobStatus>(`/jobs/${jobId}`);
        const data = resp.data;
        setStatus(data);

        if (data.status === "completed" && data.report_id) {
          // Stop polling
          if (intervalRef.current) clearInterval(intervalRef.current);
          // Fetch the report
          const reportResp = await client.get<VerificationReport>(`/reports/${data.report_id}`);
          onComplete(data, reportResp.data);
        } else if (data.status === "failed") {
          if (intervalRef.current) clearInterval(intervalRef.current);
          onFailed(data);
        }
      } catch {
        // Ignore poll errors, keep trying
      }
    }

    poll();
    intervalRef.current = setInterval(poll, 2000);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [jobId, failedStatus, onComplete, onFailed]);

  const progress = status?.progress ?? 0;
  const message = status?.progress_message || "Starting verification...";
  const isFailed = status?.status === "failed";

  return (
    <div className={styles.container}>
      <div className={styles.card}>
        {isFailed ? (
          <>
            <div className={styles.failIcon}>!</div>
            <h2 className={styles.title}>Verification Failed</h2>
            <p className={styles.errorMessage}>
              {status?.error || "An unknown error occurred."}
            </p>
            <button className={styles.retryBtn} onClick={onReset}>
              Try Again
            </button>
          </>
        ) : (
          <>
            <div className={styles.spinner} />
            <h2 className={styles.title}>Verifying Citations</h2>
            <p className={styles.message}>{message}</p>
            <div className={styles.progressTrack}>
              <div
                className={styles.progressBar}
                style={{ width: `${Math.max(progress, 2)}%` }}
              />
            </div>
            <p className={styles.percent}>{progress}%</p>
            {status?.filename && (
              <p className={styles.filename}>{status.filename}</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
