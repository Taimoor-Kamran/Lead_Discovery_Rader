import type { JobRunRead } from "@/lib/api";

export const RERUN_WARNING_DAYS = 7;
export const RERUN_WARNING =
  "This search ran within the last 7 days: re-running costs API calls and usually finds the same businesses. Run it again?";

/** Whether "Run again" should warn: the last discovery run is younger than a week. */
export function ranRecently(lastRun: JobRunRead | null | undefined, now = new Date()): boolean {
  if (!lastRun) return false;
  const stamp = new Date(lastRun.finished_at ?? lastRun.created_at).getTime();
  if (Number.isNaN(stamp)) return false;
  return now.getTime() - stamp < RERUN_WARNING_DAYS * 24 * 3600 * 1000;
}

/** The single place a re-run is confirmed, so tests can replace the dialog. */
export let confirmRerun: () => boolean = () => window.confirm(RERUN_WARNING);
export function setConfirmRerun(fn: () => boolean): void {
  confirmRerun = fn;
}
