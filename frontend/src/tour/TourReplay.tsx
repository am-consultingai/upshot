import { useI18n } from "../i18n";
import Button from "../components/Button";
import SettingRow, { SettingGroup } from "../components/SettingRow";
import { useTour } from "./TourProvider";

/** Settings → About: the tour again, from the start. */
export default function TourReplay() {
  const { t } = useI18n();
  const tour = useTour();
  return (
    <SettingGroup>
      <SettingRow label={t("tour.replayRow")} description={t("tour.replayHint")}>
        <Button data-testid="tour-replay" onClick={() => tour.start("replay")}>
          {t("tour.replay")}
        </Button>
      </SettingRow>
    </SettingGroup>
  );
}
