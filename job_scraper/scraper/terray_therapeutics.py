from job_scraper.scraper._rippling import scrape_board

scrape = scrape_board("terraytx", name="Terray Therapeutics")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
