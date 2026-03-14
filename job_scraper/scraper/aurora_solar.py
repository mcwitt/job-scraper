from job_scraper.scraper._ashby import scrape_board

scrape = scrape_board("aurorasolar", name="Aurora Solar")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
