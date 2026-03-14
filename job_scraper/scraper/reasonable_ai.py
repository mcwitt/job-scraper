from job_scraper.scraper._workable import scrape_board

scrape = scrape_board("reasonable", name="Reasonable AI")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
