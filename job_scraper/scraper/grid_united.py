from job_scraper.scraper._breezy import scrape_board

scrape = scrape_board("grid-united", name="Grid United")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
