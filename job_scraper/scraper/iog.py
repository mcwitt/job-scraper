from job_scraper.scraper._workable import scrape_board

scrape = scrape_board("io-global", name="Input Output Global")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
