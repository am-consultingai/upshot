import { useQuery } from "@tanstack/react-query";
import { api, type CalendarAccount } from "../api";
import { accountColour, accountsOf } from "../lib/calendar";

/**
 * Which calendar account something is on: a small dot per account (D82). Colour on this
 * page means state, so an account never tints a block; it only marks it. Nothing is
 * drawn while a single account is listed: there is nothing to tell apart.
 */
export default function AccountDots({
  item,
  className = "",
}: {
  item: { account_id?: string; accounts?: string[]; calendar_accounts?: string[] };
  className?: string;
}) {
  const accounts = useCalendarAccounts();
  if (accounts.length < 2) return null;
  const ids = accountsOf(item, accounts);
  if (ids.length === 0) return null;
  const byId = new Map(accounts.map((account) => [account.id, account]));
  return (
    <span className={`inline-flex flex-none items-center gap-0.5 ${className}`} data-testid="account-dots">
      {ids.map((id) => {
        const account = byId.get(id)!;
        return <AccountDot key={id} account={account} />;
      })}
    </span>
  );
}

export function AccountDot({ account, size = 6 }: { account: CalendarAccount; size?: number }) {
  return (
    <span
      aria-label={account.address}
      title={account.address}
      data-account={account.id}
      className="inline-block flex-none rounded-full"
      style={{ width: size, height: size, background: accountColour(account.color) }}
    />
  );
}

/** The listed (not removed) accounts, from the shared calendar status. */
export function useCalendarAccounts(): CalendarAccount[] {
  const status = useQuery({ queryKey: ["calendar"], queryFn: api.calendarStatus });
  return status.data?.accounts ?? [];
}
