from job_scraper.scraper._ashby import scrape_board

scrape = scrape_board("genesis-ai", name="Genesis AI")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
