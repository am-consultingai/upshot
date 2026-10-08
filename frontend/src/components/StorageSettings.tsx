import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { formatBytes } from "../lib/format";
import SettingRow, { SettingGroup } from "./SettingRow";

/**
 * Where the meetings are, and how much room they take.
 *
 * "Local only" is the product's whole claim, and until this it could not be checked
 * from inside the app: the sidebar said how much, nothing said where. The size is the
 * one the sidebar footer shows (`/status`, recounted every minute). There is nothing
 * to set here on purpose — the folder is chosen once, outside the app — so the rows
 * are statements, and the last says what happens to old recordings: nothing.
 */
export default function StorageSettings() {
  const { t } = useI18n();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const [copied, setCopied] = useState(false);
  const folder = status.data?.data_folder;
  const size = formatBytes(status.data?.storage_bytes);

  return (
    <SettingGroup>
      <SettingRow label={t("settings.dataFolder")} description={t("settings.dataFolderHint")}>
        {folder && (
          <>
            <bdi
              data-testid="storage-folder"
              dir="ltr"
              className="max-w-[22rem] truncate font-mono text-xs text-secondary select-all"
              title={folder}
            >
              {folder}
            </bdi>
            <button
              type="button"
              data-testid="storage-folder-copy"
              onClick={() => {
                void navigator.clipboard.writeText(folder).then(() => {
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 2000);
                });
              }}
              className="h-7 shrink-0 rounded-md px-2.5 text-xs text-secondary shadow-[var(--shadow-ring)] hover:bg-a-200 hover:text-primary"
            >
              {copied ? t("settings.copied") : t("settings.copyPath")}
            </button>
          </>
        )}
      </SettingRow>
      <SettingRow label={t("settings.storageUsed")} description={t("settings.storageKept")}>
        <span data-testid="storage-size" className="text-sm tabular-nums text-secondary">
          {size ?? "—"}
        </span>
      </SettingRow>
    </SettingGroup>
  );
}
