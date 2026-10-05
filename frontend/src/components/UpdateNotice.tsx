import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useI18n } from "../i18n";
import { toast } from "./Toaster";

const SHOWN = "upshot.updates.shown";

/**
 * "Updated to X" once, after an update installed itself (D87). The update happened
 * while the user was away from the window, so this is something that happened
 * elsewhere: a toast, with "What's new" opening About and updates.
 */
export default function UpdateNotice() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const updates = useQuery({ queryKey: ["updates"], queryFn: api.updates });
  const last = updates.data?.install?.last;
  const version = last?.result === "updated" ? last.to : null;

  useEffect(() => {
    if (!version || readShown() === version) return;
    writeShown(version);
    toast({
      title: t("updates.updatedTitle").replace("{version}", version),
      action: { label: t("updates.whatsNew"), run: () => navigate("/settings#about") },
    });
  }, [version, t, navigate]);

  return null;
}

function readShown(): string | null {
  try {
    return window.localStorage.getItem(SHOWN);
  } catch {
    return null;
  }
}

function writeShown(version: string): void {
  try {
    window.localStorage.setItem(SHOWN, version);
  } catch {
    // Without storage it may show once more after a reload; nothing worse.
  }
}
