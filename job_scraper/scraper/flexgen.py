from job_scraper.scraper._rippling import scrape_board

scrape = scrape_board("external-job-board", name="FlexGen")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
