import { useQuery } from "@tanstack/react-query";
import { signOut } from "../../auth";
import { me } from "../../queries/me";
import { teams } from "../../queries/teams";

export function AccountCard() {
  const { data: user } = useQuery(me);
  const { data: teamList } = useQuery(teams);
  if (!user) return null;
  const team = user.team_id ? (teamList?.find((t) => t.id === user.team_id)?.name ?? user.team_id) : "Not linked yet";
  return (
    <>
      <header className="card-head">
        <div>
          <h2>Your account</h2>
          <p className="card-sub">{user.auth ? "Signed in with Google" : "Logins aren't set up on this computer"}</p>
        </div>
      </header>
      <div>
        {user.auth && (
          <div className="account-who">
            <span className="avatar large" aria-hidden="true">
              {(user.name || user.email).trim().charAt(0).toUpperCase()}
            </span>
            <div>
              <div className="account-who-name">{user.name || user.email}</div>
              {user.name && <div className="sub">{user.email}</div>}
            </div>
          </div>
        )}
        <dl className="account-facts">
          <dt>Team</dt>
          <dd>{team}</dd>
          <dt>Role</dt>
          <dd>{user.is_admin ? "Admin" : "GM"}</dd>
        </dl>
        {user.auth ? (
          <button className="btn ghost" type="button" onClick={signOut}>
            Sign out
          </button>
        ) : (
          <p className="card-sub">
            Without GOOGLE_CLIENT_ID, everyone who opens the app is the admin, so there's nothing to sign out of.
          </p>
        )}
      </div>
    </>
  );
}
