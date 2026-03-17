from job_scraper.scraper._workable import scrape_board

scrape = scrape_board("imandra", name="Imandra")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
