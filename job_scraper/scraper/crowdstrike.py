from job_scraper.scraper._workday import scrape_board

scrape = scrape_board(
    "crowdstrike", "wd5", "crowdstrikecareers", name="CrowdStrike"
)

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
