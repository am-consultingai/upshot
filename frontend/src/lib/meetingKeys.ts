/** What the meeting page's keys do, as something the palette can ask for too. */
export type MeetingKey = "play" | "next" | "previous" | "find";

const EVENT = "upshot:meeting-key";

/** Do what the key would have done on the open meeting. Nothing, if none is open. */
export function meetingKey(key: MeetingKey): void {
  window.dispatchEvent(new CustomEvent<MeetingKey>(EVENT, { detail: key }));
}

export function onMeetingKey(handler: (key: MeetingKey) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<MeetingKey>).detail);
  window.addEventListener(EVENT, listener);
  return () => window.removeEventListener(EVENT, listener);
}
