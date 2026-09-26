import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import SetupFlow from "../setup/SetupFlow";
import { SetupBackendContext } from "../setup/backend";
import { ApiSetupBackend } from "../setup/apiBackend";

/**
 * First-run setup (epic z8tj1hb01k): Welcome → Google Calendar → AI summaries → Sound
 * check → Done, with the speech model first when the installer could not fetch it.
 *
 * The screens are the ones confirmed on the mock (Setup 0); this route gives them the
 * real backend. What the machine already has — a model, a calendar, a signed-in CLI, a
 * saved step to resume on — is read once before the first screen, because it decides
 * which steps there are. Finishing saves the choices and `setup.done`, and opens the
 * Timeline.
 */
export default function Welcome() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [backend, setBackend] = useState<ApiSetupBackend | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live: ApiSetupBackend | null = null;
    let gone = false;
    ApiSetupBackend.create()
      .then((created) => {
        if (gone) {
          created.dispose();
          return;
        }
        live = created;
        setBackend(created);
      })
      .catch((reason: unknown) => setError(String(reason)));
    return () => {
      gone = true;
      live?.dispose();
    };
  }, []);

  if (error) {
    return (
      <p data-testid="welcome-error" className="text-sm text-danger">
        {error}
      </p>
    );
  }
  if (!backend) return null;

  return (
    <SetupBackendContext.Provider value={backend}>
      <section data-testid="welcome-page" className="flex min-w-0 flex-1">
        <SetupFlow
          onFinished={async () => {
            // Set rather than invalidated: the shell reads this to decide whether to send
            // the next screen back here, and a refetch in flight would briefly say "not done".
            queryClient.setQueryData(["settings"], await api.settings());
            await queryClient.invalidateQueries({ queryKey: ["llm-status"] });
            // The shell sees this once and celebrates (Confetti); it is cleared straight
            // after, so a reload does not do it again.
            navigate("/", { replace: true, state: { celebrate: true } });
          }}
        />
      </section>
    </SetupBackendContext.Provider>
  );
}
