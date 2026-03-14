from job_scraper.scraper._workable import scrape_board

scrape = scrape_board("renewhome", name="RenewHome")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
