-- The provider a waiting job needs signed in, or NULL. Set when a stage is parked
-- because that CLI is signed out (SignInRequired); cleared by every other change of state.
-- The meeting page reads it to offer Sign in and the services that are ready, rather than
-- asking the CLI's own status, which can still say "signed in" with an expired session.
ALTER TABLE jobs ADD COLUMN waiting_for TEXT;
