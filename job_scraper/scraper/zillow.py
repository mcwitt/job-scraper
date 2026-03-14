from job_scraper.scraper._workday import scrape_board

scrape = scrape_board(
    "zillow", "wd5", "Zillow_Group_External", name="Zillow"
)

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
