from typing import List, Optional, Dict, Tuple, Any, Literal
from pydantic import BaseModel, Field, field_validator, ValidationError
from datetime import datetime
from bson import ObjectId
from datetime import datetime, timezone, timedelta
import pytz
import random

# Add this line to ensure the correct path
import sys
import os
from utils.helper_methods import convert_utc_to_local, get_day_number, get_closest_draft_date
from utils.config import db

from models import PyObjectId
from models.base_model import Base
import string

class FantasyLeagueSeasonNotFoundError(Exception):
    """Custom exception for missing fantasy league season."""
    pass

def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

class Request(Base):
    Status: Literal["approved", "pending", "declined"] = "pending"
    UserId: PyObjectId

class League(Base):
    id: Optional[PyObjectId] = Field(alias='_id')
    Name: str
    CommissionerId: PyObjectId
    LeagueSettings: Optional[Dict]
    FantasyLeagueSeasons: Optional[List[PyObjectId]] = []
    CurrentFantasyLeagueSeasonId: Optional[PyObjectId] = None
    CurrentPeriodId: PyObjectId
    WaiverOrder: Optional[List[PyObjectId]] = []
    LeagueRequests: List[Request] = []
    LeagueInvites: List[Request] = []
    Full: bool = False
    LeagueCode: str = generate_code()
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Validation to ensure the length of TeamName and LeagueName is reasonable
    @field_validator('Name')
    def validate_name_length(cls, v):
        if len(v) < 3 or len(v) > 50:  
            # Example: setting reasonable length between 3 and 50 characters
            raise ValueError('Name must be between 3 and 50 characters long.')
        return v

    def find_current_season(self):

        from models import FantasyLeagueSeason

        if self.CurrentFantasyLeagueSeasonId:
            current_season = db.fantasyLeagueSeasons.find_one({"_id": self.CurrentFantasyLeagueSeasonId})
            if current_season:
                return FantasyLeagueSeason(**current_season)
        return None

    def get_current_leagues_teams(self):
        teams = db.teams.find({"FantasyLeagueSeasonId": ObjectId(self.CurrentFantasyLeagueSeasonId)})
        return list(teams)

    def determine_current_fantasy_league_season(self) -> Optional[ObjectId]:
        current_date = datetime.now()

        for season_id in self.FantasyLeagueSeasons:
            # Assuming you have a way to get a FantasyLeagueSeason by its ID
            season = db.fantasyleagueseasons.find_one({"_id": season_id})

            if season:
                start_date = season.get("StartDate")
                end_date = season.get("EndDate")

                if start_date <= current_date <= end_date:
                    # Update the league with the current FantasyLeagueSeasonId
                    db.leagues.update_one(
                        {"_id": self.id},
                        {"$set": {"CurrentFantasyLeagueSeasonId": season_id}}
                    )
                    return season_id

        # If no current season is found, you may want to clear the current season
        db.leagues.update_one(
            {"_id": self.id},
            {"$set": {"CurrentFantasyLeagueSeasonId": None}}
        )
        return None
    
    def create_initial_season(self, tournaments) -> PyObjectId:

        from models import FantasyLeagueSeason

        if not self.FantasyLeagueSeasons or len(self.FantasyLeagueSeasons) < 1:
            if not tournaments:
                raise ValueError("No tournaments specified for the initial season.")

            first_tournament_doc = tournaments[0]
            last_tournament_doc = tournaments[-1]

            tournament_ids = [ObjectId(tournament["_id"]) for tournament in tournaments]

            first_season = FantasyLeagueSeason(
                SeasonNumber=1,
                StartDate=first_tournament_doc["StartDate"],
                EndDate=last_tournament_doc["EndDate"],
                Periods=[],
                LeagueId=self.id,
                Tournaments=tournament_ids,
                Active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )

            first_season_id = first_season.save()

            self.FantasyLeagueSeasons.append(first_season_id)
            self.CurrentFantasyLeagueSeasonId = first_season_id
            self.save()

            return first_season_id

    def prepare_transition_to_next_season(self):
        from pymongo import UpdateOne, InsertOne
        from models import FantasyLeagueSeason, Team

        # Step 1: Find the current season
        current_season = self.find_current_season()

        if not current_season:
            raise ValueError("Current season not found.")

        current_pro_season = db.proSeasons.find_one(
            {"_id": current_season.ProSeasonId},
            {"LeagueName": 1}
        )

        # Ensure there's a pro season with tournaments to renew to
        upcoming_or_ongoing_pro_season = db.proSeasons.find_one(
            {"EndDate": {"$gt": datetime.utcnow()}, "LeagueName": current_pro_season["LeagueName"]}
        )

        if not upcoming_or_ongoing_pro_season or not len(upcoming_or_ongoing_pro_season["Competitions"]) > 0:
            raise ValueError("There is not an upcoming season or there are not any upcoming tournaments.")

        # Step 2: Deactivate the current season
        deactivate_current_season_op = UpdateOne(
            {"_id": current_season.id},
            {"$set": {"Active": False}}
        )

        # Step 3: Determine the next fantasy season number
        next_season_number = current_season.SeasonNumber + 1

        # Step 4: Fetch tournaments from the current season
        prior_tournament_ids = current_season.Tournaments
        tournaments = db.tournaments.find({
            "_id": {"$in": prior_tournament_ids}
        })
        tournament_names = [tournament["Name"] for tournament in tournaments if "Name" in tournament]

        # Step 5: Query for matching tournaments for the next season
        matching_tournaments = list(db.tournaments.find({
            "Name": {"$in": tournament_names},
            "ProSeasonId": upcoming_or_ongoing_pro_season["_id"],
            "StartDate": {"$gt": datetime.utcnow()}
        }))
        matching_tournament_ids = [tournament["_id"] for tournament in matching_tournaments]

        if not matching_tournaments:
            raise ValueError("No matching tournaments found for the next season.")

        # Step 6: Prepare the next season creation operation
        first_tournament_doc = matching_tournaments[0]
        last_tournament_doc = matching_tournaments[-1]

        # Step 6: Create the next season instance and explicitly generate an _id
        next_season_id = ObjectId()
        next_season_instance = FantasyLeagueSeason(
            _id=next_season_id,
            SeasonNumber=next_season_number,
            StartDate=first_tournament_doc["StartDate"],
            EndDate=last_tournament_doc["EndDate"],
            Periods=[],
            LeagueId=self.id,
            Tournaments=matching_tournament_ids,
            Active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            ProSeasonId=upcoming_or_ongoing_pro_season["_id"]
        )

        # Convert the instance to a dictionary and set the generated _id
        next_season_dict = next_season_instance.dict(by_alias=True, exclude_unset=True)
        next_season_dict["_id"] = next_season_id  # Assign the generated ObjectId

        # Step 7: Prepare team copying operations
        teams = list(db.teams.find({"FantasyLeagueSeasonId": self.CurrentFantasyLeagueSeasonId}))
        team_operations = []
        for team_data in teams:
            team_id = ObjectId()
            new_team_instance = Team(
                _id=team_id,
                TeamName=team_data["TeamName"],
                ProfilePicture=team_data["ProfilePicture"],
                Golfers={},
                OwnerId=team_data["OwnerId"],
                LeagueId=team_data["LeagueId"],
                Points=0,
                FAAB=0,
                WaiverNumber=0,
                TeamStats={
                    "Wins": 0,
                    "TotalUnderPar": 0,
                    "AvgScore": 0.00,
                    "MissedCuts": 0,
                    "Top10s": 0
                },
                Placement=team_data["Placement"],
                FantasyLeagueSeasonId=next_season_id
            )
            new_team_data = new_team_instance.dict(by_alias=True, exclude_unset=True)
            team_operations.append(InsertOne(new_team_data))
            next_season_dict["Teams"].append(team_id)

        self.CurrentFantasyLeagueSeasonId = next_season_dict["_id"]
        self.FantasyLeagueSeasons.append(next_season_dict["_id"])

        # Return all operations
        return {
            "season_deactivation": deactivate_current_season_op,
            "team_operations": team_operations,
            "next_season_dict": next_season_dict,  # Add the next season instance
        }

    def remove_lowest_ogwr_golfer(team_id: PyObjectId) -> PyObjectId:
        # Find the team by ID
        team = db.teams.find_one({"_id": ObjectId(team_id)})
        if not team:
            raise ValueError("Team not found.")

        # Get the list of golfer IDs
        golfer_ids = team['Golfers']
        
        # Query and sort golfers by OGWR to get the lowest one
        lowest_golfer = db.golfers.find({"_id": {"$in": golfer_ids}}).sort("OGWR", 1).limit(1)
        
        lowest_golfer = list(lowest_golfer)
        if not lowest_golfer:
            raise ValueError("No golfers found in the team.")

        # Remove the golfer with the lowest OGWR from the team
        lowest_golfer_id = lowest_golfer[0]['_id']
        
        db.teams.update_one(
            {"_id": team_id},
            {"$pull": {"Golfers": lowest_golfer_id}}
        )

        return lowest_golfer_id

    def enforce_drop_deadline(self):
        if self.LeagueSettings.ForceDrops > 0:
            league_timezone = self.LeagueSettings.TimeZone
            
            # Convert current UTC time to user's local time
            utc_now = datetime.now(timezone.utc)
            local_now = convert_utc_to_local(utc_now, league_timezone)
            today_day_number = get_day_number(local_now.weekday())
            
            drop_day_number = get_day_number(self.LeagueSettings.DropDeadline)

            if today_day_number > drop_day_number:
                period = self.get_most_recent_period()
                teams = self.get_current_leagues_teams()
                team_ids = [team["_id"] for team in teams]
                for id in team_ids:
                    # force drop players if the team owner has not to do so
                    # according to the league settings force drop rules
                    while period.Drops[id] < self.LeagueSettings.ForceDrops:
                        # remove the last golfer from their team
                        self.remove_lowest_ogwr_golfer(id)

    def generate_matchups(self, period) -> List[Tuple[PyObjectId, PyObjectId]]:
        teams = self.get_current_leagues_teams()
        random.shuffle(teams)
        matchups = []

        # Dictionary to store past opponents for each team
        past_opponents = {team: set() for team in teams}

        # Populate past opponents from previous periods
        previous_periods = db.periods.find({"LeagueId": self.id, "PeriodNumber": {"$lt": period.PeriodNumber}})
        for prev_period in previous_periods:
            team_results = db.teamResults.find({"PeriodId": prev_period["_id"]})
            for result in team_results:
                team_id = result["TeamId"]
                opponent_id = result["OpponentId"]
                if opponent_id:
                    past_opponents[team_id].add(opponent_id)
                    past_opponents[opponent_id].add(team_id)

        # Create matchups ensuring no repeat until everyone has played each other
        while teams:
            team1 = teams.pop()
            possible_opponents = [t for t in teams if t not in past_opponents[team1]]
            
            if not possible_opponents:
                # All teams have played each other, reset past_opponents for new matchups
                matchups.append((team1, teams.pop()))
            else:
                team2 = random.choice(possible_opponents)
                teams.remove(team2)
                matchups.append((team1, team2))
                past_opponents[team1].add(team2)
                past_opponents[team2].add(team1)

        return matchups

    def create_initial_teams(self) -> bool:
        from models import Team

        league_settings = self.LeagueSettings
        num_of_teams = league_settings["NumberOfTeams"]
        team_ids = []

        last_fantasy_league_season_id = self.FantasyLeagueSeasons[-1]

        if last_fantasy_league_season_id:
            return False
        else:
            for i in range(num_of_teams):
                team = Team(
                    TeamName=f"Team {i+1}",
                    ProfilePicture="",
                    Golfers={},
                    OwnerId=None,
                    LeagueId=self.id,
                    DraftPicks={},
                    Points=0,
                    FAAB=0,
                    WaiverNumber=0
                )
                team.save()
                team_ids.append(team.id)
    
        self.CurrentStandings = team_ids
        self.save()
        return True

    def create_periods_between_tournaments(self, fantasy_league_season):
        from pymongo import InsertOne
        from models import Period, TeamResult, Draft

        if not fantasy_league_season:
            raise ValueError("There is no current season ongoing for this league")

        tournament_ids = fantasy_league_season["Tournaments"]
        
        tournaments = list(db.tournaments.find({"_id": {"$in": tournament_ids}}).sort("StartDate"))

        if not tournaments or len(tournaments) < 2:
            raise ValueError("Insufficient tournaments to create periods.")

        league_settings = self.LeagueSettings

        # Determine draft frequency
        draft_frequency = league_settings["DraftingFrequency"]
        draft_periods = set(range(1, len(tournaments) + 1, draft_frequency))

        # Initial period creation
        initial_period_dict = self.create_initial_period(fantasy_league_season["_id"], tournaments[0])
        
        draft_start_date = get_closest_draft_date(tournaments[0]["StartDate"], league_settings["DraftStartDayOfWeek"])

        # initial draft to go alongside period
        first_draft_dict = self.create_initial_draft(draft_start_date, initial_period_dict["_id"], league_settings["MaxGolfersPerTeam"])

        # Containers for bulk operations
        period_operations = []
        team_result_operations = []
        draft_operations = []
        period_ids = []

        period_operations.append(InsertOne(initial_period_dict))
        draft_operations.append(InsertOne(first_draft_dict))

        # Create periods, drafts, and team results
        for i in range(len(tournaments)):
            current_tournament = tournaments[i]
            start_date = self.created_at if i == 0 else tournaments[i - 1]["EndDate"]
            period_id = ObjectId()
            # Create the period
            period_data = Period(
                _id=period_id,
                LeagueId=self.id,
                StartDate=start_date,
                EndDate=current_tournament["EndDate"],
                PeriodNumber=i + 1,
                TournamentId=current_tournament["_id"],
                FantasyLeagueSeasonId=fantasy_league_season["_id"],
                Standings=[],
                TeamResults=[],
                FreeAgentSignings={}
            ).dict(by_alias=True, exclude_unset=True)

            # Assign drafts to certain periods
            if (i + 1) in draft_periods:
                draft_start_date = get_closest_draft_date(tournaments[i]["StartDate"], league_settings["DraftStartDayOfWeek"])
                draft_id = ObjectId()
                draft_data = Draft(
                    _id=draft_id,
                    LeagueId=self.id,
                    StartDate=draft_start_date,
                    Rounds=league_settings["MinFreeAgentDraftRounds"],
                    PeriodId=period_id,
                    Picks=[],
                    DraftOrder=[],
                    IsComplete=False
                ).dict(by_alias=True, exclude_unset=True)
                draft_operations.append(InsertOne(draft_data))
                period_data["DraftId"] = draft_id

            # Create team results for head-to-head leagues
            if league_settings["Game"] == "HeadToHead":
                matchups = self.generate_matchups(period_data)
                for team1_id, team2_id in matchups:
                    team_1_result_id = ObjectId()
                    team_2_result_id = ObjectId()

                    team_1_result = TeamResult(
                        _id=team_1_result_id,
                        TeamId=team1_id,
                        LeagueId=self.id,
                        TournamentId=current_tournament["_id"],
                        PeriodId=period_id,
                        TotalPoints=0,
                        GolfersScores={},
                        Placing=0,
                        PointsFromPlacing=0,
                        OpponentId=team_2_result_id
                    ).dict(by_alias=True, exclude_unset=True)
                    team_result_operations.append(InsertOne(team_1_result))
                    
                    team_2_result = TeamResult(
                        _id=team_2_result_id,
                        TeamId=team2_id,
                        LeagueId=self.id,
                        TournamentId=current_tournament["_id"],
                        PeriodId=period_id,
                        TotalPoints=0,
                        GolfersScores={},
                        Placing=0,
                        PointsFromPlacing=0,
                        OpponentId=team_1_result_id
                    ).dict(by_alias=True, exclude_unset=True)
                    team_result_operations.append(InsertOne(team_2_result))

                    period_data.setdefault("TeamResults", []).extend([team_1_result["_id"], team_2_result["_id"]])

            period_operations.append(InsertOne(period_data))
            period_ids.append(period_id)

        league_update = {
            "operation": "update_one",
            "filter": {"_id": self.id},
            "update": {"$set": {"CurrentPeriodId": period_ids[0]}}
        }

        return {
            "period_operations": period_operations,
            "draft_operations": draft_operations,
            "team_result_operations": team_result_operations,
            "league_update": league_update,
            "period_ids": period_ids
        }


    def get_most_recent_season(self):
        from models import FantasyLeagueSeason

        current_date = datetime.utcnow()
        season = db.fantasyLeagueSeasons.find_one(
            {"LeagueId": self.id, "StartDate": {"$gt": current_date}},
            sort=[("StartDate", -1)]
        )
        return FantasyLeagueSeason(**season) if season else None

    def get_most_recent_period(self):
        from models import Period

        current_period = db.periods.find_one({"_id": self.CurrentPeriodId})

        last_period = db.periods.find_one({
            "PeriodNumber": current_period - 1,
            "FantasyLeagueSeasonId": current_period["FantasyLeagueSeasonId"]
        })

        return Period(**last_period) if last_period else None

    def get_next_period(self):
        from models import Period

        current_period = db.periods.find_one({"_id": self.CurrentPeriodId})

        next_period = db.periods.find_one({
            "PeriodNumber": current_period + 1,
            "FantasyLeagueSeasonId": current_period["FantasyLeagueSeasonId"]
        })

        return Period(**next_period) if next_period else None

    def determine_waiver_order(self) -> bool:
        # Retrieve league settings
        league_settings = self.LeagueSettings

        # Check if waiver type is "Reverse Standings"
        if league_settings and league_settings["WaiverType"] == "Reverse Standings":
            # Get the most recent period
            most_recent_period = self.get_most_recent_period()

            # Ensure the most recent period and its standings exist
            if most_recent_period and most_recent_period.Standings:
                standings = most_recent_period.Standings
                self.WaiverOrder = standings[::-1]  # Reverse the standings
                self.save()

                # Update WaiverNumber for each team
                for i, team_id in enumerate(self.WaiverOrder):
                    db.teams.update_one(
                        {"_id": team_id},
                        {"$set": {"WaiverNumber": i + 1}}
                    )
                return True

        return False

    def create_initial_period(self, season_id, first_tournament): 
        from models import Period

        if not first_tournament:
            raise ValueError("No tournaments found to initialize the period.")

        # Create an initial period before the first tournament
        initial_period = Period(
            _id=ObjectId(),
            LeagueId=self.id,
            StartDate=datetime.utcnow(),
            EndDate=first_tournament["EndDate"],
            TournamentId=first_tournament["_id"],
            PeriodNumber=1,
            FantasyLeagueSeasonId=season_id
        )

        initial_period_dict = initial_period.dict(by_alias=True, exclude_unset=True)

        return initial_period_dict

    def create_initial_draft(self, draft_start_date, initial_period_id, max_golfers_per_team) -> PyObjectId:
        from models import Draft

        # Create the first draft before the first tournament
        first_draft = Draft(
            _id=ObjectId(),
            LeagueId=self.id,
            StartDate=draft_start_date,
            Rounds=max_golfers_per_team,
            PeriodId=initial_period_id,
            Picks=[],
            DraftOrder=[],
            IsComplete=False
        )

        return first_draft.dict(by_alias=True, exclude_unset=True)

    def determine_next_draft_order(self, next_period):
        from models import Draft

        last_period_standings = db.periods.find_one({"_id": self.CurrentPeriodId})["Standings"]

        next_draft = db.drafts.find_one({"_id": next_period["DraftId"]})

        if next_draft:
            next_draft_instance = Draft(**next_draft)
            next_draft_instance.DraftOrder = last_period_standings[::-1]
            
            next_draft.save()

            return True
        return False

    def start_new_period(self): 
        # get the next period
        next_period = self.get_next_period()

        # update the next draft order based on the previous period's standings
        self.determine_next_draft_order(next_period)

        # current period changes to the next period
        self.CurrentPeriodId = next_period

        if self.LeagueSettings.Waiver == "On":
            # arrange the waiver order based on however the league has decided to arrange the order in 
            self.determine_waiver_order()

    def handle_tournament_end(self, tournament_end_date: datetime):
        # Called when a tournament ends to update the current period and start a new one
        self.start_new_period(tournament_end_date)

    def save(self) -> Optional[ObjectId]:
        self.updated_at = datetime.utcnow()
        if not self.created_at:
            self.created_at = self.updated_at

        league_dict = self.dict(by_alias=True, exclude_unset=True)

        if '_id' in league_dict and league_dict['_id'] is not None:
            # Update existing document
            result = db.leagues.update_one({'_id': league_dict['_id']}, {'$set': league_dict})
            if result.matched_count == 0:
                raise ValueError("No document found with _id: {}".format(league_dict['_id']))
        else:
            # Insert new document
            result = db.leagues.insert_one(league_dict)
            self.id = result.inserted_id
        return self.id

    def get_all_rostered_players(self):
        # Collect all rostered players
        rostered_players = set()

        teams = db.teams.find({
            "FantasyLeagueSeasonId": ObjectId(self.CurrentFantasyLeagueSeasonId)
        })

        for team in teams:

            for golfer_id, golfer_info in team["Golfers"].items():
                if golfer_info['CurrentlyOnTeam']:
                    rostered_players.add(ObjectId(golfer_id))
        return rostered_players

    def get_available_golfers_for_tournament(self, tourney_id=None) -> List[dict]:
        from models import Golfer

        if self.CurrentPeriodId and not tourney_id:
            curr_period = db.periods.find_one({
                "_id": self.CurrentPeriodId
            })

            tourney_id = curr_period["TournamentId"]

        print("tourney_id: ", ObjectId(tourney_id))
        pipeline = [
            {"$match": {"TournamentId": tourney_id}},
            {
                "$lookup": {
                    "from": "golfers",
                    "localField": "GolferId",
                    "foreignField": "_id",
                    "as": "golfer"
                }
            },
            {"$unwind": "$golfer"},
            {"$sort": {"golfer.Rank": 1}}
        ]

        all_results = list(db.golfertournamentdetails.aggregate(pipeline))
        # Get all rostered GolferIds
        rostered_players = self.get_all_rostered_players()

        print(Golfer(**all_results[0]["golfer"]), all_results[1]["Name"])

        # Filter out rostered golfers
        available_golfers = [
            Golfer(**(doc["golfer"])).to_dict() for doc in all_results
            if doc["GolferId"] not in rostered_players
        ]

        # 1) find the first index where Rank is a real int
        first_valid_idx = next(
        (
            i
            for i, g in enumerate(available_golfers)
            if isinstance(g.get("Rank", None), int)
        ),
        len(available_golfers),
)
        # 2) slice into the NA chunk and the valid chunk
        na_chunk    = available_golfers[:first_valid_idx]
        valid_chunk = available_golfers[first_valid_idx:]

        # 3) recombine: valid ranks first, then all the NAs
        available_golfers = valid_chunk + na_chunk
 
        return available_golfers

    def get_available_golfers(self, limit: int, page: int = 1) -> List[dict]:
        from models import Golfer

        unavailable_players = self.get_all_rostered_players()

        # Calculate the number of documents to skip
        offset = page * limit

        # Fetch golfers who have both an OWGR and FedexPts, skipping previous pages and limiting the result to `amount`
        available_golfers_cursor = db.golfers.find(
            {
                '_id': {'$nin': list(unavailable_players)},                'FedexPts': {'$exists': True, '$ne': None}
            }
        ).sort('FedexPts', -1).skip(offset).limit(limit)

        # Count the total number of available golfers with OWGR and FedexPts
        number_of_total_available_golfers = db.golfers.count_documents({
            '_id': {'$nin': list(unavailable_players)},
            'FedexPts': {'$exists': True, '$ne': None}
        })

        # Convert to a list of full documents and ensure all attributes are JSON-serializable
        available_golfers = []
        for golfer in available_golfers_cursor:
            cleaned_golfer = {key.strip('"'): value for key, value in golfer.items()}
            golfer = Golfer(**cleaned_golfer)
            available_golfers.append(golfer.to_dict())  # Convert each golfer to a JSON-compatible dict

        return available_golfers, number_of_total_available_golfers

    class Config:
        populate_by_name = True