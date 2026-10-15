"""HTTP clients for the Fantrax public API and the NHL.com stats API."""

import json
import time
from typing import Any, Dict, List, Optional

import requests

USER_AGENT = "cfl-manager/0.1 (personal fantasy league analysis)"


class _Http:
    def __init__(self, base_url: str, timeout: float = 30.0, retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = "{}/{}".format(self.base_url, path.lstrip("/"))
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise requests.HTTPError("HTTP {}".format(resp.status_code), response=resp)
                resp.raise_for_status()
                # Decode from bytes: Fantrax omits a charset, which makes requests fall back to latin-1.
                return json.loads(resp.content)
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
                retryable = not isinstance(exc, requests.HTTPError) or (
                    exc.response is not None and (exc.response.status_code >= 500 or exc.response.status_code == 429)
                )
                if attempt == self.retries or not retryable:
                    raise
                time.sleep(1.5 * attempt)


class FantraxClient:
    """Fantrax public REST API (https://www.fantrax.com/developer). No auth needed for league reads."""

    def __init__(self, league_id: str):
        self.league_id = league_id
        self.http = _Http("https://www.fantrax.com/fxea/general")

    def league_info(self, exclude_player_info: bool = False) -> Dict[str, Any]:
        params: Dict[str, Any] = {"leagueId": self.league_id}
        if exclude_player_info:
            params["excludePlayerInfo"] = "true"
        return self.http.get("getLeagueInfo", params)

    def team_rosters(self, period: Optional[int] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"leagueId": self.league_id}
        if period is not None:
            params["period"] = period
        return self.http.get("getTeamRosters", params)

    def standings(self) -> List[Dict[str, Any]]:
        return self.http.get("getStandings", {"leagueId": self.league_id})

    def matchup_scores(self, period: Optional[int] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"leagueId": self.league_id}
        if period is not None:
            params["period"] = period
        return self.http.get("getMatchupScores", params)

    def player_ids(self, sport: str = "NHL") -> Dict[str, Dict[str, Any]]:
        return self.http.get("getPlayerIds", {"sport": sport})

    def adp(self, sport: str = "NHL") -> List[Dict[str, Any]]:
        return self.http.get("getAdp", {"sport": sport})


class NHLStatsClient:
    """NHL.com stats REST API (api.nhle.com/stats/rest). Public, no auth."""

    REGULAR_SEASON = 2

    def __init__(self):
        self.http = _Http("https://api.nhle.com/stats/rest/en")
        self.web = _Http("https://api-web.nhle.com/v1")

    def _report(self, report: str, season_id: int) -> List[Dict[str, Any]]:
        data = self.http.get(
            report,
            {
                "limit": -1,
                "start": 0,
                "cayenneExp": "seasonId={} and gameTypeId={}".format(season_id, self.REGULAR_SEASON),
            },
        )
        return data.get("data", [])

    def skater_stats(self, season_id: int) -> List[Dict[str, Any]]:
        """Skater summary joined with the 'realtime' report (hits, blocks)."""
        summary = self._report("skater/summary", season_id)
        realtime = {r["playerId"]: r for r in self._report("skater/realtime", season_id)}
        merged = []
        for s in summary:
            rt = realtime.get(s["playerId"], {})
            merged.append({**s, "hits": rt.get("hits"), "blockedShots": rt.get("blockedShots")})
        return merged

    def goalie_stats(self, season_id: int) -> List[Dict[str, Any]]:
        return self._report("goalie/summary", season_id)

    def schedule_week(self, day: str) -> Dict[str, Any]:
        """League schedule for the week starting at `day` (YYYY-MM-DD); includes nextStartDate."""
        return self.web.get("schedule/{}".format(day))
