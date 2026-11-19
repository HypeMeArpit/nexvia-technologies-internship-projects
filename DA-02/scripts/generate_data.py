"""
Generates the two synthetic tables this project needs and loads them into BigQuery:

  raw_data.ad_spend      daily spend, impressions and clicks per marketing channel (October 2026)
  raw_data.crm_profiles  one CRM profile per thelook_ecommerce user

Everything is seeded, so re-running the script produces the same data.
"""
import os
import random
from datetime import datetime, timedelta

import pandas as pd
import pandas_gbq
from faker import Faker

PROJECT_ID = "nexvia-analytics-project"

# Use GOOGLE_APPLICATION_CREDENTIALS if it is set; otherwise fall back to gcp-key.json
# in the repo root (that file is gitignored and never committed).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_PATH = os.path.join(BASE_DIR, "gcp-key.json")
if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ and os.path.exists(KEY_PATH):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = KEY_PATH

random.seed(42)
Faker.seed(42)
fake = Faker()

# ---------------------------------------------------------------------------
# 1. Ad spend
# ---------------------------------------------------------------------------
# These are the channels thelook's web traffic can be mapped to (see seeds/channel_mapping.csv).
# Each paid channel gets a planned daily budget; real spend varies +/-20% around it.
# Organic Search is unpaid, so its spend is always 0.
CHANNELS = {
    #  channel           daily budget   CTR range
    "Google Ads":     {"budget": 700, "ctr": (0.020, 0.045)},
    "Facebook Ads":   {"budget": 450, "ctr": (0.008, 0.020)},
    "Email":          {"budget": 150, "ctr": (0.025, 0.050)},
    "Organic Search": {"budget": 0,   "ctr": (0.030, 0.060)},
}

START_DATE = datetime(2026, 10, 1)
NUM_DAYS = 31  # 1 Oct - 31 Oct 2026, fixed so the dashboards don't shift between runs

print("Generating ad spend data...")
ad_spend_rows = []
for day in range(NUM_DAYS):
    spend_date = START_DATE + timedelta(days=day)
    for channel, settings in CHANNELS.items():
        impressions = random.randint(5000, 50000)
        clicks = int(impressions * random.uniform(*settings["ctr"]))
        spend = round(settings["budget"] * random.uniform(0.8, 1.2), 2)
        ad_spend_rows.append({
            "date": spend_date.date(),
            "campaign_channel": channel,
            "spend": spend,
            "impressions": impressions,
            "clicks": clicks,
        })

df_ad_spend = pd.DataFrame(ad_spend_rows)
df_ad_spend["date"] = pd.to_datetime(df_ad_spend["date"])

# ---------------------------------------------------------------------------
# 2. CRM profiles, keyed to real thelook user ids so they join to the public data
# ---------------------------------------------------------------------------
print("Fetching user ids from thelook_ecommerce...")
users_query = """
    select id
    from `bigquery-public-data.thelook_ecommerce.users`
    order by id
"""
df_users = pandas_gbq.read_gbq(users_query, project_id=PROJECT_ID)
user_ids = df_users["id"].tolist()
print(f"Fetched {len(user_ids)} user ids. Generating CRM profiles...")

PERSONAS = ["Budget Shopper", "Impulse Buyer", "Brand Loyalist", "Window Shopper"]
ACCOUNT_MANAGERS = [fake.name() for _ in range(8)]  # a small team, not one manager per customer

df_crm = pd.DataFrame({
    "user_id": user_ids,
    "customer_persona": [random.choice(PERSONAS) for _ in user_ids],
    "account_manager": [random.choice(ACCOUNT_MANAGERS) for _ in user_ids],
    "loyalty_score": [random.randint(1, 100) for _ in user_ids],
})

# ---------------------------------------------------------------------------
# 3. Load to BigQuery (full refresh)
# ---------------------------------------------------------------------------
print("Uploading to BigQuery...")
pandas_gbq.to_gbq(df_ad_spend, "raw_data.ad_spend", project_id=PROJECT_ID, if_exists="replace")
print(f"raw_data.ad_spend loaded: {len(df_ad_spend)} rows")

pandas_gbq.to_gbq(df_crm, "raw_data.crm_profiles", project_id=PROJECT_ID, if_exists="replace")
print(f"raw_data.crm_profiles loaded: {len(df_crm)} rows")
