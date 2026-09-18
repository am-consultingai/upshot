import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";

/**
 * Says so when the app behind the page has stopped answering.
 *
 * Every page used to fail into "Something went wrong", which reads as a bug in the page
 * rather than what it is: the application is no longer there. That is exactly what was on
 * screen when the process died inside a Windows notification call, and it sent nobody to
 * the window that started it.
 */
export default function ConnectionBanner() {
  const { t } = useI18n();
  const status = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: 5000,
  });
  if (!status.isError) return null;

  return (
    <div
      data-testid="connection-lost"
      role="alert"
      className="border-b border-warning bg-warning-quiet"
    >
      <p className="mx-auto max-w-5xl px-4 py-2 text-sm text-warning">
        {t("app.offline")}
      </p>
    </div>
  );
}
