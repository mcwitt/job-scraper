from job_scraper.scraper._workday import scrape_board

scrape = scrape_board("nvidia", "wd5", "NVIDIAExternalCareerSite", name="NVIDIA")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
