from job_scraper.scraper._ashby import scrape_board

scrape = scrape_board("Hippocratic AI", name="Hippocratic AI")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
