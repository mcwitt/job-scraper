from job_scraper.scraper._workday import scrape_board

scrape = scrape_board(
    "salesforce", "wd12", "External_Career_Site", name="Salesforce"
)

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
