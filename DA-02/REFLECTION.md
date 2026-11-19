# Individual reflection – Arpit Rastogi

## What I owned

I worked on this project on my own, so I owned it end to end. I chose `thelook_ecommerce` from the dataset options because its users, orders and web events all share a `user_id`, which made it possible to join everything into one customer view. I wrote the Python script that generates the synthetic ad-spend and CRM tables and loads them into BigQuery, set up the dbt project with staging, intermediate and mart models, and built the first versions of the session, attribution and RFM models.

I also built all three Tableau dashboards, made the architecture diagram, ran and debugged the pipeline on my machine, and recorded the demo.

I used an AI assistant to review my project. It pointed out problems in my logic and helped me write the linear and time-decay attribution models, the RFM scores and CLV calculation. I ran and tested all of it myself, checked the numbers against BigQuery.

## What I learned

- **dbt and data modelling:** Before this project I mostly wrote single SQL queries. Splitting the work into staging, intermediate and mart models made each step small and easy to check. I also learned that tests are what make numbers trustworthy: my reconciliation test checks that every attribution model hands out exactly October's $387,121, so I know no revenue is lost or counted twice.
- **Attribution:** I learned that "which channel brought this sale?" has no single answer. First touch, last touch, linear and time decay each answer a slightly different question. I expected the four models to disagree a lot, but they mostly agreed. Email and Google Ads brought about 85% of revenue under all of them, which made my recommendations easier to defend.
- **Customer analytics:** Building RFM scores taught me to check the data before choosing a method. Most customers have only one or two orders, so splitting frequency into quintiles would have put identical customers into different groups. I used the order count capped at 5 instead.
- **Tableau:** My first dashboards only showed spend and CTR, which didn't answer any business question. I learned to write titles that state the finding (for example "Facebook Ads costs $61 per new customer, 14x Email") and to build each dashboard around one question.

## Challenges and how I solved them

1. **dbt would not run.** First it could not find `dbt_project.yml`, because I was running commands from the wrong folder. Then it could not find my GCP key, because `profiles.yml` still pointed to an old folder. I fixed both and wrote the correct setup steps into the README so someone else won't hit the same errors.
2. **My synthetic data gave misleading results.** I had generated fully random ad spend, including Twitter, which had no matching traffic in thelook. Twitter showed −100% ROI and Organic Search showed a $0 cost per customer, which looked like findings but were just side effects of my data. I switched to planned daily budgets per channel, removed Twitter, and left CPA blank for unpaid channels.
3. **A chart that looked fine but was wrong.** My ROAS chart stacked the four attribution models into one bar, so Email looked like it returned about 151x. I changed it to one bar per model. This taught me to check whether a chart's numbers make sense, not just whether it renders.

## What I would do differently

I would plan the dashboards and the business questions first, before writing the models. I built some tables that the dashboards never used, and my first dashboards didn't use the attribution and RFM tables at all. I would also set up a clean project folder and Git from day one.

## Next steps

With real data, I would load actual ad spend joined to orders by campaign or UTM, add product cost so ROI is based on profit instead of revenue, and run a holdout test before moving any budget. I would also schedule `dbt build` to run daily so the dashboards stay up to date.
