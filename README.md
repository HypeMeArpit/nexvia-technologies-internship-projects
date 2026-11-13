# Nexvia Technologies — Data Analyst Internship Projects

**Author:** Arpit Rastogi · Data Analyst Intern, Nexvia Technologies

This repository holds both projects allotted to me during the internship. Each project
lives in its own folder with its own README, source code, tests, documentation and screenshots.

| Code | Project | What it does | Stack |
|---|---|---|---|
| [DA-02](./DA-02) | Customer 360 & Marketing Attribution Platform | Unifies CRM, web-event and ad-spend data into a tested analytics warehouse with RFM segmentation and first/last-touch attribution, plus executive dashboards | Python, BigQuery, dbt Core, Tableau |
| [DA-04](./DA-04) | Financial Analytics and Risk Reporting System | Automated finance reporting for a lender: P&L, balance sheet, budget vs actual with variance drivers, a WoE + logistic-regression credit scorecard, interactive dashboards and an emailed monthly board pack | Python, MySQL, Excel Power Pivot, Qlik Sense, Power Automate |

## Repository layout

```
.
├── DA-02/   Customer 360 & Marketing Attribution (full commit history preserved)
└── DA-04/   Financial Analytics and Risk Reporting System
```

Start with each project's own `README.md` for architecture, setup and run steps, and results.

## Notes for reviewers

- No credentials are committed. Each project reads secrets from local files (`.env`, a GCP key) that are excluded by `.gitignore`; templates such as `.env.example` show what is needed.
- Large raw datasets are not committed; each README explains how to download them.
