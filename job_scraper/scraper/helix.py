from job_scraper.scraper._gem import scrape_board

scrape = scrape_board("helix", name="Helix")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
