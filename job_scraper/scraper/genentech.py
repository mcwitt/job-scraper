from job_scraper.scraper._workday import scrape_board

scrape = scrape_board("roche", "wd3", "ROG-A2O-GENE", name="Genentech")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
