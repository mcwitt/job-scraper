from job_scraper.scraper._workday import scrape_board

scrape = scrape_board("nextracker", "wd5", "nextpower_careers", name="Nextracker")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
