from job_scraper.scraper._ashby import scrape_board

scrape = scrape_board("formenergy", name="Form Energy")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
