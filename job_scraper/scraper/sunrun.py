from job_scraper.scraper._workday import scrape_board

scrape = scrape_board("sunrun", "wd5", "Sunrun_Careers", name="Sunrun")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
