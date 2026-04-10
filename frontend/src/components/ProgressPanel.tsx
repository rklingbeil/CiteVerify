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
  const [pollError, setPollError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const errorCountRef = useRef(0);
  const onCompleteRef = useRef(onComplete);
  const onFailedRef = useRef(onFailed);
  onCompleteRef.current = onComplete;
  onFailedRef.current = onFailed;

  useEffect(() => {
    if (failedStatus) return; // Don't poll if we already have a failure

    async function poll() {
      try {
        const resp = await client.get<JobStatus>(`/jobs/${jobId}`);
        const data = resp.data;
        setStatus(data);
        errorCountRef.current = 0; // Reset on success

        if (data.status === "completed" && data.report_id) {
          // Stop polling
          if (intervalRef.current) clearInterval(intervalRef.current);
          // Fetch the report with retry
          for (let attempt = 0; attempt < 3; attempt++) {
            try {
              const reportResp = await client.get<VerificationReport>(`/reports/${data.report_id}`);
              onCompleteRef.current(data, reportResp.data);
              return;
            } catch {
              if (attempt < 2) await new Promise(r => setTimeout(r, 2000));
            }
          }
          setPollError("Report generated but could not be loaded. Please try again.");
        } else if (data.status === "failed") {
          if (intervalRef.current) clearInterval(intervalRef.current);
          onFailedRef.current(data);
        }
      } catch {
        errorCountRef.current++;
        if (errorCountRef.current >= 10) {
          if (intervalRef.current) clearInterval(intervalRef.current);
          setPollError("Lost connection to server. The verification may still be running — try refreshing the page.");
        }
      }
    }

    poll();
    intervalRef.current = setInterval(poll, 2000);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [jobId, failedStatus]);

  const progress = status?.progress ?? 0;
  const message = status?.progress_message || "Starting verification...";
  const isFailed = status?.status === "failed";
  const hasError = isFailed || pollError;

  return (
    <div className={styles.container}>
      <div className={styles.card}>
        {hasError ? (
          <>
            <div className={styles.failIcon}>!</div>
            <h2 className={styles.title}>{isFailed ? "Verification Failed" : "Connection Error"}</h2>
            <p className={styles.errorMessage}>
              {pollError || status?.error || "An unknown error occurred."}
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
