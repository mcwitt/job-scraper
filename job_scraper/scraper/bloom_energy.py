from job_scraper.scraper._workday import scrape_board

scrape = scrape_board("bloomenergy", "wd1", "BloomEnergyCareers", name="Bloom Energy")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
