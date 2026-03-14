from job_scraper.scraper._workday import scrape_board

scrape = scrape_board("stem", "wd12", "StemInc", name="Stem Inc")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
