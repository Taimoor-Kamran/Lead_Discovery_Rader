import { SafeLink } from "@/components/SafeLink";
import { Card } from "@/components/ui";
import type { LinkedProfiles, SourceRecord } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { socialPlatformLabel, sourceNameLabel } from "@/lib/labels";

type Props = {
  sources: SourceRecord[];
  linkedProfiles: LinkedProfiles;
};

/**
 * "Where this came from" — the answer to the question a prospect actually asks, in one
 * sentence per source, without opening a developer table.
 *
 * Two things are deliberately kept apart on this card:
 *
 * 1. **Sources.** One line per discovered record the business was built from — not per
 *    survivorship winner — naming the source in plain words with the raw code in the
 *    tooltip, linking to the record, and giving both when it was first found and when the
 *    source last handed it over again.
 * 2. **Links found on their own website.** The audit records which platforms the business's
 *    homepage points at. Those are the business's own links, never something this system
 *    read, and the heading and the sentence under it have to say so before anyone asks —
 *    which is why this block never appears under the "sources" heading.
 *
 * On paper the sources list stays — a rep asked "where did you get my details?" on a call
 * needs it there — while the linked-profiles block drops out with the rest of the screen-only
 * chrome: it reassures the person reading the record, but it is not part of the argument.
 */
export function SourceProvenance({ sources, linkedProfiles }: Props) {
  // Both blocks tolerate a response that omits them: a missing provenance block is a page
  // with nothing to attribute, not a white screen.
  const records = sources ?? [];
  const profiles = linkedProfiles?.profiles ?? [];
  return (
    <Card className="print-break-avoid print-plain" data-testid="source-provenance">
      <h2 className="text-md font-semibold text-ink">Where this came from</h2>

      {records.length ? (
        <ul className="mt-2 flex flex-col gap-2 text-base" data-testid="source-list">
          {records.map((source) => (
            <li key={`${source.code}-${source.source_record_id}`} data-testid="source-entry">
              <span className="font-medium text-ink" title={source.code} data-testid="source-name">
                {sourceNameLabel(source.code)}
              </span>
              <dl className="mt-0.5 grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-0.5 text-sm text-ink-soft">
                <dt>First found</dt>
                <dd data-testid="source-discovered">{formatDateTime(source.discovered_at)}</dd>
                <dt>Last seen</dt>
                <dd data-testid="source-last-seen">{formatDateTime(source.last_seen_at)}</dd>
                <dt>Their record</dt>
                <dd className="break-words">
                  {source.source_url ? (
                    <SafeLink href={source.source_url}>{source.source_record_id}</SafeLink>
                  ) : (
                    <span className="font-mono">{source.source_record_id}</span>
                  )}
                </dd>
              </dl>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-base text-ink-soft">
          No source record is on file for this business, so there is nothing to attribute its
          details to.
        </p>
      )}

      {profiles.length ? (
        <div className="print-hide mt-4 border-t border-line pt-3" data-testid="linked-profiles">
          <h3 className="text-base font-semibold text-ink">Linked from their website</h3>
          <p className="mt-1 max-w-measure text-sm text-ink-soft">
            Profiles their own homepage links to. We recorded the links while auditing{" "}
            {linkedProfiles.page_url ? (
              <SafeLink href={linkedProfiles.page_url} />
            ) : (
              "their homepage"
            )}
            . None of these profiles was opened or read.
          </p>
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-base">
            {profiles.map((profile) => (
              <li key={profile.platform} data-testid="linked-profile">
                <span title={profile.platform}>{socialPlatformLabel(profile.platform)}</span>
                {profile.url ? (
                  <>
                    {" "}
                    <SafeLink href={profile.url} />
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

/**
 * The compact form for a list row: one line per source name, raw code in the tooltip. A
 * business with no record on file says so rather than leaving the cell blank, which would
 * read as "not loaded yet".
 */
export function SourceCell({ codes }: { codes: string[] }) {
  const shown = codes ?? [];
  if (!shown.length) return <span className="text-ink-soft">no record</span>;
  return (
    <ul className="flex flex-wrap gap-1" data-testid="source-cell">
      {shown.map((code) => (
        <li key={code} className="text-sm" title={code} data-testid="source-cell-item">
          {sourceNameLabel(code)}
        </li>
      ))}
    </ul>
  );
}
