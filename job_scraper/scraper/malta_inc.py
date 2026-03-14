from job_scraper.scraper._workable import scrape_board

scrape = scrape_board("malta-inc", name="Malta Inc")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
