import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import Banner from "./Banner";

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
    <Banner data-testid="connection-lost" role="alert" tone="attention">
      <span className="text-warning">{t("app.offline")}</span>
    </Banner>
  );
}
